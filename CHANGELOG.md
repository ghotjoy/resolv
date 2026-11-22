# Resolv release changelog



## Resolv release

- Renamed the application, Python domain module, configuration keys, defaults and browser labels to Resolv.
- Updated the README, documentation, report, demonstration script and screenshots across the release.
- Existing incident data can be reused by pointing the new configuration at the existing database and evidence directory. See the setup guide for local demo data migration.

- Rechecked 59 unit tests and all five API suites on the renamed release. Used Edge to sign in, create sample incidents and capture the employee, intake and analytics screens.

## GitHub documentation



- Reworked the README around the live campus workflow, role capabilities, design decisions and verified tests.

- Added three screenshots captured from the actual web app with sample incidents, and moved extended setup and integration instructions into docs/setup.md.

- Included the code-aligned final report and the detailed 18-minute presentation script.



## Employee export and OOP clarity



- Removed Export CSV from the employee queue and personal analytics. The API also denies employee CSV requests. Support roles still export only incidents they can view.

- Moved incident visibility rules into the User hierarchy. The base class supplies the common visibility check, concrete roles supply ownership behavior and escalation thresholds, and export defaults to denied unless the role opts in. The browser receives the server's export permission.

- Added concrete examples of encapsulation, abstraction, inheritance and polymorphism to the README and project report.

- Verified this revision with all 59 unit tests and the follow-up API suite, including export checks for every role. An Edge smoke test confirmed no employee export in either view, a visible technician export, and a working administrator download.



## Startup and account access



- Documented the seeded evaluator path, `python start_demo.py`. The bare server prints bootstrap instructions when no administrator exists.

- Legacy demo accounts with the old shipped password require an explicitly configured replacement. Startup no longer generates an undisclosed replacement password.

- The demo launcher checks that the printed password works for the evaluator accounts. `python start_demo.py --reset-demo-password` resets only the six original demo users in the launcher database. It preserves incident data and settings.

- Sign-in trims whitespace and accepts a unique case-insensitive match. Existing IDs that differ only by case still accept their exact spelling. New accounts cannot introduce such collisions.

- New IDs allow 2 to 20 characters. New passwords require 12 to 256 characters across registration, administrator setup and recovery. Existing non-legacy passwords continue to work.

- Login failures are limited per address and per account/address pair. Successful sign-ins do not consume failed-attempt quota. An exhausted account/address pair still needs to wait, while the same account can sign in from a different address that has quota.



## Workflow and usability



- Expired and invalidated sessions return the browser to sign-in with an explanation. This includes a server restart.

- API errors show readable messages. When a same-reporter duplicate is blocked, the form offers to open the existing ticket for follow-up. Reports from separate people remain eligible for relation review.

- Scheduled SLA checks run every 30 seconds by default. Set `RESOLV_MONITOR_SECONDS=0` for manual monitoring. The README explains working calendars, server-local time and UTF-8 CSV import.

- Plain SMTP no longer initializes a TLS context. SQLite connections used by the mail worker and test fixtures close explicitly.



## Documentation and validation



- Updated the report, setup instructions and demonstration guide. The release includes the four-presenter script with the library Wi-Fi, F102 smartboard and Vishwakarma Bhavan workflows.

- Added a targeted login and duplicate-report API regression suite and a GitHub Actions workflow. Older audit and viva PDFs are marked historical in the README.

- Verified on Windows with Python 3.12.14 and local loopback enabled. All 59 unit tests passed, including local SMTP delivery and retry. All five API suites passed: core, login, enhancements, recommendations and follow-up.

- Startup checks also passed for an empty database, demo password mismatch detection and reset, and explicit legacy password migration.

- A real Edge smoke test passed for admin, technician and employee sign-in, evidence upload, the duplicate-report shortcut, and the return to sign-in after a restart. This was a targeted browser run. The three packaged Python Playwright suites were not run locally.

- Still to verify in the intended environment: hosted CI, Docker startup, live SMTP provider delivery, voice transcription or browser dictation, live Gemini, Excel import and operation across time zones. The local-rule triage and CSV API checks passed.

- Before submission, add the actual group number, member names and IDs, contributions and repository URL to the report. The archive provides neither those details nor the recorded video. Record and submit the video separately using the course instructions.

