# Test catalogue

| Case | Scenario | Expected result | Suite |
| --- | --- | --- | --- |
| 01 | Ordinary workflow skips acknowledgement | Invalid transition is rejected | Core unit and API |
| 02 | Administrator closes a verified case | Closure time and audit event are recorded | Core API |
| 03 | Wrong technician attempts an action | Request is forbidden | Core API |
| 04 | Employee accesses another reporter's issue | Request is forbidden | Recommendations API |
| 05 | Reporter tries to upload a disallowed file | Upload is rejected | Enhancements API |
| 06 | Audit event is hidden and restored | Original event stays available to authorized reviewers | Enhancements API |
| 07 | Existing SQLite audit schema is migrated | Stable event ID survives reopening and vacuum | Enhancements unit |
| 08 | Incidents are submitted concurrently | Every accepted report has a unique ID | Recommendations API |
| 09 | Reports arrive before and after deadline | Warning and breach events are distinct | Recommendations API |
| 10 | Resolution SLA crosses three levels | Each tier produces one event | Enhancements unit |
| 11 | Tier lead attempts technician assignment | Request is forbidden | Follow-up API |
| 12 | Manager opens a higher-tier case | Case and tier notification are visible to the manager | Follow-up API |
| 13 | Employee requests a CSV and a support user exports a formula-like title | Employee gets 403. Support export stays scoped and escapes the title | Follow-up API |
| 14 | Administrator dismisses a possible duplicate | Pair is retained for review and removed from active suggestions | Follow-up API |
| 15 | SLA spans a weekend and holiday | Deadline advances through working time only | Follow-up unit |
| 16 | Policy is edited after intake | Existing incident retains its dates | Follow-up unit and API |
| 17 | Assignment strategy fails during intake | Classified report and audit trail are retained | Follow-up unit |
| 18 | Recovery code is reused or expired | Request is rejected | Follow-up unit and API |
| 19 | Password changes through recovery | Prior sessions are invalidated | Follow-up API |
| 20 | Provider is disabled or proposes an invalid category | Local rules return a valid advisory result | Follow-up unit |
| 21 | Employee requests access through the browser | Approval, code redemption and sign-in complete | Follow-up browser |
| 22 | Employee opens queue and analytics, then administrator downloads CSV | Employee sees no export button. Admin gets the correct file and columns | Follow-up browser |
| 23 | Employee records a voice note | Local model transcribes the recording for review | Enhancements browser |
| 24 | Container starts with a fresh named volume | Health check and HTTP response succeed | Compose smoke check |

Run `python3 -m unittest`, then the HTTP and browser suites described in the README. The follow-up suites use isolated databases. Tests for Gemini use an injected transport and never require a live key.
