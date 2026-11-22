# Resolv follow-up implementation

## 1. Problem and scope

This revision keeps the Python and SQLite service-desk application and applies the third review's patterns to the existing incident lifecycle. It adds role-scoped reporting, account recovery, escalation tiers, configurable intelligence, business-time SLA clocks, and container startup.

## 2. Requirements and permissions

| Role | Incident access | Escalation | Account access |
| --- | --- | --- | --- |
| Employee | Own reports, attachments and analytics | Receives updates | Request access or recovery |
| Technician | Assigned incidents and workload | Receives updates | Request recovery |
| Team Lead | Incidents at tier 1 or above | May advance a visible open incident | Request recovery |
| Administrator | All incidents, settings and audit | May advance any open incident | Review access and recovery requests |
| IT Manager | Incidents at tier 3 | Receives final-tier updates | Request recovery |

Employee analytics remain available. Incident CSV export is limited to
technicians, team leads, IT managers and administrators, within their
existing visibility scope. Both the queue and analytics display the same
server-provided export permission.

Account approval requires identity verification via the team's established channel. The application displays a one-time code to the reviewing administrator. The code digest and expiry are stored until the applicant sets a password. Recovery invalidates sessions using the old password. Requests receive the same public acknowledgment for existing and unknown IDs.

## 3. Architecture

```mermaid
flowchart LR
  Browser --> HTTP["HTTP and authentication"]
  HTTP --> Desk["Incident and reporting services"]
  HTTP --> Accounts["Account access service"]
  HTTP --> Triage["Triage service"]
  Desk --> Repo["SQLite repository"]
  Accounts --> Repo
  Desk --> Events["Domain events"]
  Events --> Audit["Append-only audit"]
  Events --> Notifications["In-app records and SMTP adapter"]
  Triage --> Rules["Local rules"]
  Triage --> Provider["Optional Gemini adapter"]
```

## 4. Main workflows

Intake validates the draft, selects a category and priority, snapshots the SLA and commits the report and audit trail. Auto-assignment runs in a savepoint. Failed assignment leaves a classified case for later dispatch.

```mermaid
sequenceDiagram
  participant Applicant
  participant Desk
  participant Admin
  Applicant->>Desk: Request access or recovery
  Admin->>Desk: Verify identity and approve
  Desk-->>Admin: Issue a code once
  Admin-->>Applicant: Share code privately
  Applicant->>Desk: Redeem code and set password
  Desk-->>Applicant: Enable sign-in
```

## 5. Domain design

The incident state classes stamp assignment, response, resolution, verification and closure dates. Force transitions remain privileged, reasoned and audited. The existing closed-incident override begins a new SLA cycle. Manual and automatic escalations share the same one-tier transition and persisted event key.

Technician assignment uses a shared eligibility filter and four strategies. Preview ranks eligible candidates without advancing the round-robin cursor. The assignment event stores its reason.

## 6. Scoring, correlation and SLA

The brief formula uses impact × urgency × criticality, with thresholds 27, 15 and 6. The app maps impact and urgency level 4 to level 3 for this formula, and service criticality level 4 to level 5. The category-weighted strategy uses site-scale severity, a configurable category factor, and bonuses of 2, 4, 8 and 12 for 5, 25, 100 and 500 affected users. It uses thresholds 36, 18 and 7. The historical scoring option retains the earlier severity product with its 1.25 multiplier for 100 or more affected users.

Relationship scoring uses 72% text overlap, 20% shared service and 8% shared category. Suggested pairs are stored once, and reviewers can confirm or dismiss them. The default related threshold is 0.40. Same-service matches reaching 0.72 are marked as possible duplicates. Incident links do not merge records. Same-reporter duplicate protection remains in place.

The SLA schedule can run continuously or use working days, hours and holidays in server time. Changes apply to new SLA cycles. Reprioritization retains the cycle's working calendar. The monitor records response and resolution warnings and breaches independently, including late completion detected after the fact. Stored risk is the last monitored value. Views also calculate current risk.

## 7. Technology selection and deployment

Python's standard library covers the server, SQLite, CSV, email, and provider adapter. This keeps the application easy to start without a separate database service or build system. Compose supplies an isolated app process and persistent data volume. Browser assets retain the existing CSP and role checks. The optional provider receives only explicitly opted-in triage messages and the available category names.

## 8. Database design

The migration runner applies ordered SQL migrations under a write transaction using SQLite's schema version. It adopts existing unversioned installations and retains audit IDs. Large fields and the incident aggregate remain in the existing JSON record. Search pushes stable filters and incident visibility into SQLite. Current SLA status is evaluated using the applicable incident deadlines afterward.

```mermaid
erDiagram
  USER ||--o{ INCIDENT : reports
  INCIDENT ||--o{ SLA_EVENT : has
  INCIDENT ||--o{ INCIDENT_RELATIONSHIP : relates
  INCIDENT ||--o{ AUDIT_EVENT : records
  USER ||--o{ ACCOUNT_REQUEST : requests
```

The SLA event key is unique per incident and cycle. The relationship table uses a canonical incident pair. Account recovery tokens are stored as digests and expire on the server's real clock.
